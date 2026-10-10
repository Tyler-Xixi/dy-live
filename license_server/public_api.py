from dataclasses import asdict
from typing import Annotated
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from license_protocol import PRODUCT, verify_document, validate_claims
from .cards import LicenseDenied

TextId = Annotated[str, Field(min_length=1, max_length=128, strict=True)]
Nonce = Annotated[str, Field(min_length=24, max_length=128, strict=True)]


class ActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card: TextId
    device_id: TextId
    client_version: Annotated[str, Field(min_length=1, max_length=64, strict=True)]
    nonce: Nonce


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: dict
    device_id: TextId
    client_version: Annotated[str, Field(min_length=1, max_length=64, strict=True)]
    nonce: Nonce


def router():
    api = APIRouter(prefix="/api/v1")

    def reply(request, body, check=False):
        state = request.app.state
        try:
            if check:
                payload = verify_document(body.credential, state.public_keys)
                claims = validate_claims(payload, body.device_id, state.clock.wall_time(), enforce_expiry=False)
                claims = state.cards.check(claims, body.device_id)
            else:
                claims = state.cards.bind(body.card, body.device_id)
            result, credential = "allow", state.signer.sign(asdict(claims))
        except LicenseDenied as exc:
            if exc.reason == "unknown_card": raise HTTPException(400, "invalid_card")
            result, credential = exc.reason, None
        except ValueError:
            raise HTTPException(400, "invalid_request")
        return state.signer.sign(dict(version=1, product=PRODUCT, nonce=body.nonce,
                                      server_time=state.clock.wall_time(), result=result, credential=credential))

    @api.post("/activate")
    def activate(body: ActivationRequest, request: Request):
        return reply(request, body)

    @api.post("/check")
    def check(body: CheckRequest, request: Request):
        return reply(request, body, check=True)

    return api
