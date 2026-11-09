import threading

from flask import Flask, request as flask_request
from werkzeug.serving import make_server


class OAuthCallbackServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self.host = host
        self.port = port
        self.auth_code_data: dict[str, str] = {}
        self.app = Flask(__name__)
        self.app.add_url_rule("/", "oauth_redirect", self._oauth_redirect)

        self._callback_event = threading.Event()
        self._start_lock = threading.Lock()
        self._started = False

    def _oauth_redirect(self):
        self.auth_code_data["url"] = flask_request.url
        self._callback_event.set()
        return """
        <html>
          <head>
            <style>
              body {
                background: #0d0d0d;
                color: #e0e0e0;
                font-family: monospace;
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100vh;
                margin: 0;
              }
              h1 {
                font-size: 1.4rem;
                letter-spacing: .15em;
                border: 1px solid #444;
                padding: 2rem 3rem;
              }
            </style>
          </head>
          <body>
            <h1>✓ Authentication successful — you may close this window.</h1>
          </body>
        </html>
        """

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._started = True
            threading.Thread(
                target=self._run,
                daemon=True,
                name="OAuthCallbackServer",
            ).start()

    def _run(self) -> None:
        make_server(self.host, self.port, self.app, threaded=True).serve_forever()

    def reset(self) -> None:
        self.auth_code_data.clear()
        self._callback_event.clear()

    def callback_url(self) -> str | None:
        return self.auth_code_data.get("url")

    def wait_for_callback(self, timeout: float = 300.0) -> str | None:
        if not self._callback_event.wait(timeout):
            return None
        return self.callback_url()


oauth_server = OAuthCallbackServer()
