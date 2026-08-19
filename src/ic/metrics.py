from prometheus_client import Counter, Histogram

INGEST_RUNS = Counter(
    "ic_ingest_runs_total", "Completed Ingest runs", labelnames=("endpoint", "health")
)
INGEST_DOCUMENTS = Counter(
    "ic_ingest_documents_total", "RawDocuments observed by Ingest", labelnames=("endpoint",)
)
INGEST_ITEMS = Counter(
    "ic_ingest_items_total", "Item outcomes from Ingest", labelnames=("endpoint", "outcome")
)
INGEST_ERRORS = Counter(
    "ic_ingest_errors_total", "Typed Ingest errors", labelnames=("endpoint", "error_type")
)
INGEST_DURATION = Histogram(
    "ic_ingest_duration_seconds", "Ingest run wall time", labelnames=("endpoint",)
)
