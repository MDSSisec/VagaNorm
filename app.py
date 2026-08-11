from __future__ import annotations

import threading
import webbrowser

from vaganorm import create_app


app = create_app()


if __name__ == "__main__":
    port = 5051
    url = f"http://127.0.0.1:{port}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"\nVagaNorm2 iniciado em {url}\nPressione Ctrl+C para encerrar.\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
