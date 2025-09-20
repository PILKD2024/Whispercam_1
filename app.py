import base64
import binascii
import json
import os

import psycopg2
from flask import Flask, request, jsonify, send_from_directory
from pywebpush import WebPushException, webpush
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from py_vapid.utils import b64urlencode

app = Flask(__name__, static_folder='icons', static_url_path='/icons')

# In-memory storage for Push API subscriptions
subscriptions = []

DEFAULT_VAPID_PRIVATE_KEY = "6RjkQd0fQFOdq6bfDmcnSO0_RttJnuXOXgkcB3hAgpw"
DEFAULT_VAPID_PUBLIC_KEY = "BOGW8OYirbHr3fN1S7ry7qkrvJhf7DhK--TBhE_JbkdGvOFZhl0pDueg6NA_RXS8BQ6nIzYWZH9nlyuidyZMHtA"


def _derive_public_key(private_key: str) -> str | None:
    if not private_key:
        return None
    try:
        vapid = Vapid.from_string(private_key)
    except Exception:
        return None
    public_bytes = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return b64urlencode(public_bytes)


def _decode_base64url(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _is_apns_endpoint(hostname: str) -> bool:
    return hostname in {"api.push.apple.com", "api.sandbox.push.apple.com"}


VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", DEFAULT_VAPID_PRIVATE_KEY)
_derived_public = _derive_public_key(VAPID_PRIVATE_KEY)
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", _derived_public or DEFAULT_VAPID_PUBLIC_KEY)
VAPID_CLAIMS = {"sub": os.getenv("VAPID_EMAIL", "mailto:example@example.com")}

# 1. Configure your PostgreSQL database connection
#    Replace with your actual database credentials
DB_HOST = 'dpg-cttejurqf0us73erd8s0-a'
DB_NAME = 'database_aicamera'
DB_USER = 'database_aicamera_user'
DB_PASSWORD = 'K3qqkaQzJoSLEhOsMIOhlVYi9ktIaANz'
DB_PORT = 5432

# 2. Connect to the PostgreSQL database
def get_db_connection():
    conn = psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT
    )
    return conn


conn = psycopg2.connect(
    host=DB_HOST,
    database=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
    port=DB_PORT
)
command = """CREATE TABLE IF NOT EXISTS generated_images (
            id SERIAL PRIMARY KEY,
            original_image_data BYTEA,
            generated_image_data BYTEA,
            prompt_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
try:
    # read the connection parameters
    cur = conn.cursor()
    # create table one by one
    cur.execute(command)
    # close communication with the PostgreSQL database server
    cur.close()
    # commit the changes
    conn.commit()
except (Exception, psycopg2.DatabaseError) as error:
    print(error)

@app.route('/')
def index():
    """Serve the main index.html or your frontend entry."""
    return send_from_directory('.', 'index.html')  # Adjust path as needed


@app.route('/index.html')
def index_html():
    """Serve index.html when the file path is requested explicitly."""
    return send_from_directory('.', 'index.html')


@app.route('/vapid-public-key', methods=['GET'])
def get_vapid_public_key():
    """Expose the configured VAPID public key for the frontend."""
    if not VAPID_PUBLIC_KEY:
        return jsonify({'error': 'VAPID public key is not configured.'}), 500
    return jsonify({'publicKey': VAPID_PUBLIC_KEY})


@app.route('/save_image', methods=['POST'])
def save_image():
    """Save the original camera image along with optional prompt text."""
    try:
        original_file = request.files.get('original_image')
        prompt_text = request.form.get('prompt', '')

        if not original_file:
            return jsonify({
                'status': 'error',
                'message': 'Missing original_image file.'
            }), 400

        original_data = original_file.read()

        conn = get_db_connection()
        cur = conn.cursor()

        # Insert into DB (generated_image_data left NULL)
        query = """
            INSERT INTO generated_images (original_image_data, prompt_text)
            VALUES (%s, %s)
            RETURNING id;
        """
        cur.execute(query, (psycopg2.Binary(original_data), prompt_text))
        new_id = cur.fetchone()[0]

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({'status': 'ok', 'id': new_id})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/get_images', methods=['GET'])
def get_images():
    """Fetch saved images and prompt text."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, original_image_data, prompt_text
            FROM generated_images
            ORDER BY created_at DESC
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        results = []
        for row in rows:
            row_id = row[0]
            orig_data = row[1]
            prompt_text = row[2] or ""

            orig_b64 = base64.b64encode(orig_data).decode('utf-8') if orig_data else None

            results.append({
                'id': row_id,
                'original_image_base64': orig_b64,
                'prompt_text': prompt_text
            })
        return jsonify(results)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/delete_image/<int:image_id>', methods=['DELETE'])
def delete_image(image_id):
    """Delete an image and its associated text by id."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM generated_images WHERE id = %s", (image_id,)
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if deleted:
            return jsonify({'status': 'ok'})
        else:
            return jsonify({'status': 'error', 'message': 'Image not found'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/subscribe', methods=['POST'])
def subscribe():
    """Store a Push API subscription."""
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return jsonify({'status': 'error', 'message': 'Push notifications are not configured.'}), 500

    subscription = request.get_json(silent=True) or {}
    endpoint = subscription.get('endpoint')
    keys = subscription.get('keys') if isinstance(subscription.get('keys'), dict) else {}
    p256dh = keys.get('p256dh') if isinstance(keys, dict) else None
    auth_secret = keys.get('auth') if isinstance(keys, dict) else None

    if not endpoint or not p256dh or not auth_secret:
        return jsonify({'status': 'error', 'message': 'Invalid subscription payload.'}), 400

    try:
        _decode_base64url(p256dh)
        _decode_base64url(auth_secret)
    except (binascii.Error, ValueError):
        return jsonify({'status': 'error', 'message': 'Subscription keys are not valid base64url strings.'}), 400

    subscriptions[:] = [sub for sub in subscriptions if sub.get('endpoint') != endpoint]
    subscriptions.append(subscription)

    return jsonify({'status': 'ok', 'message': 'Subscription stored.'})


@app.route('/broadcast', methods=['POST'])
def broadcast():
    """Send a push notification to all stored subscriptions."""
    if not VAPID_PRIVATE_KEY:
        return jsonify({'status': 'error', 'message': 'VAPID private key is not configured.'}), 500

    data = request.get_json() or {}
    title = data.get('title', '')
    body = data.get('body', '')
    payload = json.dumps({'title': title, 'body': body})

    for sub in list(subscriptions):
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
            )
        except WebPushException as exc:
            print(f"Web push failed: {exc}")

    return jsonify({'status': 'sent'})


@app.route('/history.html')
def serve_history():
    return send_from_directory('.', 'history.html')

@app.route('/manifest.webmanifest')
def manifest():
    return send_from_directory('.', 'manifest.webmanifest')

@app.route('/sw.js')
def service_worker():
    return send_from_directory('.', 'sw.js')


if __name__ == '__main__':
    # 3. Run the Flask app (default: localhost:5000)
    app.run(host="0.0.0.0", port=10000, debug=True)
