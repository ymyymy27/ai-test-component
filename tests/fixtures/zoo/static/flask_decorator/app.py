from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route("/api/items", methods=["GET", "POST"])
def items_collection():
    if request.method == "POST":
        return jsonify({"id": "1", "title": request.json.get("title")}), 201
    return jsonify([])


@app.route("/api/items/<item_id>", methods=["GET", "PUT", "DELETE"])
def item_detail(item_id: str):
    return jsonify({"id": item_id})
