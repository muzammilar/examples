from flask import Flask, jsonify

def create_app():
    app = Flask(__name__)

    @app.route('/')
    def home():
        return jsonify(message="Hello, Flask!")

    @app.route('/greet/<name>')
    def greet(name):
        return jsonify(message=f"Hello, {name}!")

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
