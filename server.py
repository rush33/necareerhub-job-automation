"""
server.py
FastAPI Microservice with Real-Time Log Streaming and Form Inputs.
"""

import os
import json
import asyncio
import logging
import tempfile
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# Import your pipeline
from job_automation_v2 import run_pipeline, parse_links

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom log handler to capture pipeline logs and push them to an async queue
# Custom log handler to capture pipeline logs and push them to an async queue
class QueueLoggingHandler(logging.Handler):
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.queue = queue
        self.loop = loop  # Store the main thread's event loop!

    def emit(self, record):
        msg = self.format(record)
        # Use the stored loop to safely inject logs from the background thread
        self.loop.call_soon_threadsafe(self.queue.put_nowait, msg)

@app.post("/api/process-job")
async def process_job(
    publish: str = Form("false"),  # React sends booleans as strings in FormData
    links: str = Form("[]"),
    source_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    # Convert string boolean from React
    is_publish = publish.lower() == "true"

    try:
        # Load the raw string array from React
        raw_links_list = json.loads(links)
        # Run it through your pipeline's parser to turn it into dictionaries!
        parsed_links = parse_links(raw_links_list)
    except Exception as e:
        logging.error(f"Failed to parse links: {e}")
        parsed_links = []

    tmp_path = None
    pdf_paths = None

    # Handle if they uploaded a PDF
    if file and file.filename:
        if not file.filename.endswith(".pdf"):
            raise HTTPException(status_code=400, detail="File must be a PDF")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(await file.read())
        tmp.close()
        tmp_path = tmp.name
        pdf_paths = [tmp_path]
        
    # Validation: Must have either a file or a URL
    if not pdf_paths and not source_url:
        raise HTTPException(status_code=400, detail="Must provide either a PDF file or a Source URL")

    # Setup the live log stream
    loop = asyncio.get_running_loop() # Grab the main event loop HERE
    log_queue = asyncio.Queue()
    handler = QueueLoggingHandler(log_queue, loop) # Pass the loop into the handler
    handler.setFormatter(logging.Formatter('%(message)s'))
    
    pipeline_logger = logging.getLogger("job_automation_v2")
    pipeline_logger.addHandler(handler)

    async def log_generator():
        yield "📡 Connection established. Handing off to AI worker thread...\n\n"
        
        def run_task():
            try:
                payload = run_pipeline(
                    pdf_paths=pdf_paths, 
                    source_url=source_url,
                    links=parsed_links,
                    publish=is_publish, 
                    output_dir="./api_output"
                )
                return payload
            except Exception as e:
                logging.error(f"❌ PIPELINE ERROR: {e}")
                return None
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)

        task = loop.run_in_executor(None, run_task)

        # While the task is running, yield logs from the queue to the frontend
        while not task.done() or not log_queue.empty():
            try:
                # Wait for a log message, timeout allows checking if task is done
                msg = await asyncio.wait_for(log_queue.get(), timeout=0.5)
                yield f"{msg}\n"
            except asyncio.TimeoutError:
                continue

        # Clean up the logger once done
        pipeline_logger.removeHandler(handler)
        yield "\n✅ [PROCESS_COMPLETE]"

    return StreamingResponse(log_generator(), media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)