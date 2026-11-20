"""Adaptador local: expone la MISMA función Lambda por HTTP para Docker Compose
(en la nube la invoca API Gateway). Así el código probado localmente es el que se despliega."""

from fastapi import FastAPI, Request, Response

from handler import lambda_handler

app = FastAPI(title="flags-admin (adaptador local de la Lambda)")


@app.get("/health/live")
@app.get("/health/ready")
def health() -> dict:
    return {"status": "UP"}


@app.api_route("/flags", methods=["GET"])
@app.api_route("/flags/{name}", methods=["GET", "PUT"])
async def proxy(request: Request) -> Response:
    event = {"httpMethod": request.method, "path": request.url.path, "headers": dict(request.headers),
             "body": (await request.body()).decode() or None}
    result = lambda_handler(event)
    return Response(content=result["body"], status_code=result["statusCode"], media_type="application/json")
