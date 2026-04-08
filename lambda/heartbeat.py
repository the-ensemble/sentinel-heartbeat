import json
import datetime
import os


def handler(event, context):
    return {
        "statusCode": 200,
        "body": json.dumps({
            "status": "healthy",
            "region": os.environ.get("AWS_REGION", "unknown"),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })
    }
