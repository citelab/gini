import socket
import sys
from flask import Flask

app = Flask(__name__)


@app.route('/')
def hello():
    hostname = socket.gethostname()
    ip = socket.gethostbyname(hostname)
    return f"Hostname: {hostname}\n" + \
           f"IPv4: {ip}\n"


if __name__ == '__main__':
    port_to_listen = int(sys.argv[1])
    app.run(host='0.0.0.0', port=port_to_listen, threaded=True)
