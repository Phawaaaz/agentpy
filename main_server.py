"""Entry point for the HTTP API server:  python main_server.py
(or, for production:  uvicorn interfaces.server:app --host 0.0.0.0 --port 8000)
"""

from interfaces.server import main

if __name__ == "__main__":
    main()
