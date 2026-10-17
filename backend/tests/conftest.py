import os

# Settings are required at import time; tests never reach the real project.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("SERVICE_API_KEY", "test-worker-key")
