"""
Operator authentication routes: check status, unlock with the PIN, and exit
operator mode. Operator state lives in the session cookie.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from ...config_manager import ConfigManager
from ..dependencies import check_operator, get_config_manager
from ..models import OperatorAuthRequest

router = APIRouter()


@router.get("/api/auth/operator")
def check_operator_status(request: Request):
    """Check if user is currently authenticated as operator."""
    is_operator = check_operator(request)
    return {"operator": is_operator}


@router.post("/api/auth/operator")
def authenticate_operator(
    request: Request,
    auth_data: OperatorAuthRequest,
    config: ConfigManager = Depends(get_config_manager),
):
    """Authenticate as operator with PIN."""
    correct_pin = config.get("operator_pin", "1234")
    if auth_data.pin == correct_pin:
        request.session["operator"] = True
        return {"status": "authenticated", "operator": True}
    else:
        raise HTTPException(status_code=401, detail="Invalid PIN")


@router.post("/api/auth/logout")
def logout_operator(request: Request):
    """Exit operator mode."""
    request.session["operator"] = False
    return {"status": "logged_out", "operator": False}
