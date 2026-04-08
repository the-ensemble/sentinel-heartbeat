import json
import os
import datetime
import azure.functions as func

app = func.FunctionApp()


@app.route(route="heartbeat", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def heartbeat(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({
            "status": "healthy",
            "provider": "azure",
            "region": os.environ.get("REGION_NAME", "unknown"),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }),
        status_code=200,
        mimetype="application/json",
    )
