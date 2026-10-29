"""Prometheus metrics and monitoring."""
from prometheus_client import Counter, Histogram, Gauge, Summary, CollectorRegistry, generate_latest
from typing import Dict, Any, Optional
from datetime import datetime
import time
import asyncio
import aiohttp
import json


class MetricsCollector:
    """Prometheus metrics for the crawler."""
    
    def __init__(self, namespace: str = "crawler"):
        self.namespace = namespace
        self.registry = CollectorRegistry()
        
        # Counters
        self.requests_total = Counter(
            'requests_total', 'Total HTTP requests',
            ['method', 'status', 'domain'], namespace=namespace, registry=self.registry
        )
        self.items_scraped = Counter(
            'items_scraped_total', 'Total items scraped',
            ['domain', 'status'], namespace=namespace, registry=self.registry
        )
        self.errors_total = Counter(
            'errors_total', 'Total errors',
            ['type', 'domain'], namespace=namespace, registry=self.registry
        )
        self.queue_size = Gauge(
            'queue_size', 'Current queue size',
            ['status'], namespace=namespace, registry=self.registry
        )
        self.active_workers = Gauge(
            'active_workers', 'Number of active workers',
            namespace=namespace, registry=self.registry
        )
        
        # Histograms
        self.request_duration = Histogram(
            'request_duration_seconds', 'Request duration',
            ['method', 'domain'], namespace=namespace, registry=self.registry,
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
        )
        self.parse_duration = Histogram(
            'parse_duration_seconds', 'Parse duration',
            ['domain'], namespace=namespace, registry=self.registry,
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
        )
        self.item_size = Histogram(
            'item_size_bytes', 'Scraped item size',
            ['domain'], namespace=namespace, registry=self.registry,
            buckets=[100, 500, 1000, 5000, 10000, 50000, 100000]
        )
        
        # Summary
        self.retries = Summary(
            'retries_per_request', 'Retries per request',
            ['domain'], namespace=namespace, registry=self.registry
        )
        
        # Custom metrics
        self.custom_metrics: Dict[str, Any] = {}
    
    def record_request(self, method: str, status: int, domain: str, duration: float):
        self.requests_total.labels(method=method, status=str(status), domain=domain).inc()
        self.request_duration.labels(method=method, domain=domain).observe(duration)
        self.queue_size.labels(status="processing").inc()
    
    def record_item(self, domain: str, status: str = "success", size: int = 0):
        self.items_scraped.labels(domain=domain, status=status).inc()
        if size:
            self.item_size.labels(domain=domain).observe(size)
    
    def record_error(self, error_type: str, domain: str = "unknown"):
        self.errors_total.labels(type=error_type, domain=domain).inc()
    
    def record_retry(self, domain: str, count: int):
        self.retries.labels(domain=domain).observe(count)
    
    def set_workers(self, count: int):
        self.active_workers.set(count)
    
    def set_queue_size(self, status: str, size: int):
        self.queue_size.labels(status=status).set(size)
    
    def get_metrics(self) -> bytes:
        return generate_latest(self.registry)
    
    def get_metrics_text(self) -> str:
        return self.get_metrics().decode('utf-8')


class WebhookNotifier:
    """Webhook notifications for crawl events."""
    
    def __init__(self, webhook_url: str = None, secret: str = None):
        self.webhook_url = webhook_url
        self.secret = secret
        self.session = None
    
    async def _get_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session
    
    def _sign_payload(self, payload: dict) -> str:
        if not self.secret:
            return ""
        import hmac
        import hashlib
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        return hmac.new(self.secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    
    async def notify(self, event: str, data: dict):
        if not self.webhook_url:
            return
        
        payload = {
            "event": event,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data
        }
        
        signature = self._sign_payload(payload)
        headers = {"Content-Type": "application/json"}
        if signature:
            headers["X-Signature"] = signature
        
        try:
            session = await self._get_session()
            async with session.post(self.webhook_url, json=payload, headers=headers) as resp:
                return resp.status
        except Exception as e:
            return None
    
    async def notify_crawl_started(self, job_id: str, config: dict):
        await self.notify("crawl.started", {"job_id": job_id, "config": config})
    
    async def notify_crawl_completed(self, job_id: str, stats: dict):
        await self.notify("crawl.completed", {"job_id": job_id, "stats": stats})
    
    async def notify_crawl_failed(self, job_id: str, error: str):
        await self.notify("crawl.failed", {"job_id": job_id, "error": error})
    
    async def notify_items_batch(self, job_id: str, items: list, count: int):
        await self.notify("items.batch", {"job_id": job_id, "count": count, "items": items[:10]})
    
    async def close(self):
        if self.session:
            await self.session.close()


class MetricsServer:
    """HTTP server for Prometheus metrics."""
    
    def __init__(self, metrics_collector: MetricsCollector, port: int = 9090):
        self.metrics_collector = metrics_collector
        self.port = port
        self.app = None
        self.runner = None
    
    async def start(self):
        from aiohttp import web
        self.app = web.Application()
        self.app.router.add_get('/metrics', self.metrics_handler)
        self.app.router.add_get('/health', self.health_handler)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '0.0.0.0', self.port)
        await site.start()
    
    async def metrics_handler(self, request):
        from aiohttp import web
        return web.Response(
            text=self.metrics_collector.get_metrics_text(),
            content_type='text/plain; version=0.0.4; charset=utf-8'
        )
    
    async def health_handler(self, request):
        from aiohttp import web
        return web.json_response({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})
    
    async def stop(self):
        if self.runner:
            await self.runner.cleanup()


# OpenTelemetry tracing setup
class TracingManager:
    """OpenTelemetry distributed tracing."""
    
    def __init__(self, service_name: str = "crawler", exporter_endpoint: str = None):
        self.service_name = service_name
        self.exporter_endpoint = exporter_endpoint
        self.tracer = None
        self._initialized = False
    
    def initialize(self):
        if self._initialized:
            return
        
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.resources import Resource
            
            if self.exporter_endpoint:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                exporter = OTLPSpanExporter(endpoint=self.exporter_endpoint)
            else:
                from opentelemetry.exporter.jaeger.thrift import JaegerExporter
                exporter = JaegerExporter(
                    agent_host_name="localhost",
                    agent_port=6831,
                )
            
            resource = Resource.create({"service.name": self.service_name})
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            
            self.tracer = trace.get_tracer(self.service_name)
            self._initialized = True
        except ImportError:
            pass
    
    def start_span(self, name: str, attributes: dict = None):
        if not self._initialized or not self.tracer:
            return DummySpan()
        span = self.tracer.start_span(name)
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        return span
    
    def trace_request(self, url: str, method: str):
        return self.start_span(f"http.{method}", {
            "http.url": url,
            "http.method": method,
        })
    
    def trace_parse(self, url: str):
        return self.start_span("parse", {"url": url})


class DummySpan:
    """Dummy span for when tracing is not available."""
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def set_attribute(self, k, v): pass
    def end(self): pass