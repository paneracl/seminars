import json
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# Load seminar data
def load_seminars():
    with open('seminars.json', 'r', encoding='utf-8') as file:
        return json.load(file)

# Save seminar data
def save_seminars(seminars):
    with open('seminars.json', 'w', encoding='utf-8') as file:
        json.dump(seminars, file, ensure_ascii=False)

# Load registrations
def load_registrations():
    try:
        with open('registrations.json', 'r', encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        return []

# Save registrations
def save_registrations(registrations):
    with open('registrations.json', 'w', encoding='utf-8') as file:
        json.dump(registrations, file, ensure_ascii=False)

# Route to serve the HTML form
@app.route('/')
def home():
    return render_template('index.html')

# Route to get seminars
@app.route('/seminars', methods=['GET'])
def get_seminars():
    seminars = load_seminars()
    return jsonify(seminars)

# Route to handle registration
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    location = data.get('location')
    seminar_id = int(data.get('seminarId'))

    seminars = load_seminars()
    seminar_list = seminars.get(location, [])

    # Find the selected seminar
    seminar = next((s for s in seminar_list if s['id'] == seminar_id), None)

    if seminar and seminar['slots'] > 0:
        # Reduce slots by 1
        seminar['slots'] -= 1
        save_seminars(seminars)

        # Save the registration
        registrations = load_registrations()
        registrations.append({
            "name": data['name'],
            "email": data['email'],
            "seminar": seminar['name'],
            "location": location
        })
        save_registrations(registrations)

        return jsonify({'message': 'Ευχαριστούμε για την εγγραφή σας!'}), 200
    else:
        return jsonify({'error': 'Το σεμινάριο είναι πλήρες.'}), 400

# Run the app
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
