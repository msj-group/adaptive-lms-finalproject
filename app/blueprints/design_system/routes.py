from flask import Blueprint, render_template

design_system_bp = Blueprint("design_system", __name__, url_prefix="/design-system")


@design_system_bp.get("/")
def index():
    return render_template("design_system/index.html")
