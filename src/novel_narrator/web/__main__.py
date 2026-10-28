import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8000


def main():
    webbrowser.open(f"http://{HOST}:{PORT}")
    uvicorn.run("novel_narrator.web.app:create_app", factory=True, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
