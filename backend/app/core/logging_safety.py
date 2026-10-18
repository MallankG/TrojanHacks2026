"""Redact credentials and URL query payloads before any application log is formatted."""
import logging
import re

JWT = re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b')
AUTH = re.compile(r'(?i)\b(Bearer\s+)[^\s,;\"\']+')
SECRET = re.compile(r'(?i)((?:password|service_role_key|service_api_key|access_token|refresh_token|authorization|api_key)[\"\']?\s*[:=]\s*[\"\']?)[^\s,;\"\']+')
QUERY = re.compile(r'(https?://[^\s?\"\']+)\?[^\s\"\']*')
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')


def redact(text):
    text=JWT.sub('[redacted token]',str(text))
    text=AUTH.sub(r'\1[redacted]',text)
    text=SECRET.sub(r'\1[redacted]',text)
    text=QUERY.sub(r'\1?[redacted query]',text)
    return EMAIL.sub('[redacted email]',text)


_FORMATTER = logging.Formatter()


def install():
    previous=logging.getLogRecordFactory()
    def factory(*args,**kwargs):
        record=previous(*args,**kwargs)
        try:
            record.msg=redact(record.getMessage())
        except Exception:
            return record  # bad format args: let Handler.handleError report it, as logging normally does
        record.args=()
        if record.exc_info and record.exc_info[0] is not None:
            # Keep the traceback for diagnosis, redacted like the message.
            record.exc_text=redact(_FORMATTER.formatException(record.exc_info))
        record.exc_info=None
        return record
    logging.setLogRecordFactory(factory)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('uvicorn.access').disabled=True
