

# Store timestamps as local business time consistently on every deployment.
import os
from datetime import datetime
from zoneinfo import ZoneInfo

TAX_RATE = 0.0825

BUSINESS_TIMEZONE = os.environ.get("TCCP_TIMEZONE", "America/Chicago")

def business_now():
    return datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).replace(tzinfo=None)
