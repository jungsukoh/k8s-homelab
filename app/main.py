from flask import Flask
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import os, time

app = Flask(__name__)
VERSION = os.getenv("APP_VERSION", "v1")

REQUEST_COUNT   = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'HTTP request latency', ['endpoint'])

@app.route('/')
def hello():
    start = time.time()
    REQUEST_COUNT.labels(method='GET', endpoint='/', status='200').inc()
    REQUEST_LATENCY.labels(endpoint='/').observe(time.time() - start)
    return f"Hello from {VERSION}! - Auto CI/CD Test\n"

@app.route('/health')
def health():
    return "ok", 200

@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)