from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape

app = Flask(__name__)


@app.route("/")
def hello_world():
    env = Environment(
        loader=FileSystemLoader("."),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("graph.jinja2")
    return template.render(hourly_step_data=[0, 2000, 4654, 5800, 6000])


if __name__ == "__main__":
    app.run(debug=True)
