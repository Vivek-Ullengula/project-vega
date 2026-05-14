# main.py
"""Entry point for the Coaction Agent Platform."""

from dotenv import load_dotenv

load_dotenv()  # Load .env before anything else

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
