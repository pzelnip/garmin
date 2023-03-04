import json

from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape

app = Flask(__name__)


@app.route("/")
def step_progress():
    with open("dailysteps.json", "r") as fobj:
        data = json.load(fobj)

    env = Environment(
        loader=FileSystemLoader("."),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template("graph.jinja2").render(hourly_step_data=data["step_data"])


if __name__ == "__main__":
    app.run(debug=True)
