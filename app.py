import json

from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape

from db import _init_db, DayStats


app = Flask(__name__)
engine = _init_db()


@app.route("/test")
def test():
    from sqlmodel import Session, select

    with Session(engine, expire_on_commit=False) as session:
        stmt = select(DayStats).order_by(DayStats.day)
        results = session.exec(stmt)

        content = "<pre>"
        for result in results:
            content += f"{result.day} {result.step_count}\n"
        content += "</pre>"
    return content


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
    app.run(debug=True, port=9329)
