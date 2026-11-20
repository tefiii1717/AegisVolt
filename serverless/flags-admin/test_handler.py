import importlib
import json
import os

import boto3
import pytest
from moto import mock_aws


@pytest.fixture(params=["dev", "prod"])
def handler(request):
    os.environ.update(AEGIS_ENV=request.param, FLAGS_TABLE=f"aegisvolt-feature-flags-{request.param}",
                      AWS_DEFAULT_REGION="us-east-1")
    with mock_aws():
        ddb = boto3.client("dynamodb")
        ddb.create_table(TableName=os.environ["FLAGS_TABLE"], BillingMode="PAY_PER_REQUEST",
                         AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                               {"AttributeName": "sk", "AttributeType": "S"}],
                         KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                                    {"AttributeName": "sk", "KeyType": "RANGE"}])
        for name, locked in (("SIMULATE_CHARGER_HARDWARE_FAULT", True), ("ENABLE_DYNAMIC_SURGE_PRICING", False)):
            ddb.put_item(TableName=os.environ["FLAGS_TABLE"], Item={
                "pk": {"S": f"FLAG#{name}"}, "sk": {"S": "FLAG"}, "enabled": {"BOOL": False},
                "prod_locked": {"BOOL": locked}})
        import handler as module

        module = importlib.reload(module)
        yield module, request.param


def call(mod, method, path, body=None):
    res = mod.lambda_handler({"httpMethod": method, "path": path, "headers": {"X-Aegis-Actor": "tester"},
                              "body": json.dumps(body) if body is not None else None})
    return res["statusCode"], json.loads(res["body"])


def test_lista_y_cambia_sin_redesplegar(handler):
    mod, env = handler
    status, body = call(mod, "PUT", "/flags/ENABLE_DYNAMIC_SURGE_PRICING", {"enabled": True})
    assert status == 200 and body["enabled"] is True and body["updated_by"] == "tester"
    _, listing = call(mod, "GET", "/flags")
    assert listing["environment"] == env and len(listing["flags"]) == 2


def test_flag_de_fallos_bloqueada_solo_en_prod(handler):
    mod, env = handler
    status, _ = call(mod, "PUT", "/flags/SIMULATE_CHARGER_HARDWARE_FAULT", {"enabled": True})
    assert status == (403 if env == "prod" else 200)


@pytest.mark.parametrize("body", [{"enabled": "yes"}, {"enabled": True, "extra": 1}, []])
def test_valida_el_cuerpo(handler, body):
    mod, _ = handler
    status, _ = call(mod, "PUT", "/flags/ENABLE_DYNAMIC_SURGE_PRICING", body)
    assert status == 400


def test_nombre_invalido(handler):
    mod, _ = handler
    assert call(mod, "GET", "/flags/../../etc")[0] in (400, 404)
