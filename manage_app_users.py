import json
import os
from werkzeug.security import generate_password_hash

USERS_FILE = 'data/users.json'

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)

def add_user(username, password):
    users = load_users()
    if username in users:
        print(f"⚠️ User '{username}' already exists.")
        return
    users[username] = {
        "u": username,
        "p": generate_password_hash(password)
    }
    save_users(users)
    print(f"🔧 User '{username}' was sucessfully added.")

def update_password(username, new_password):
    users = load_users()
    if username not in users:
        print(f"⚠️ User '{username}' does not exist.")
        return
    users[username]["p"] = generate_password_hash(new_password)
    save_users(users)
    print(f"🔧 Password for user '{username}' sucessfully updated.")

if __name__ == "__main__":
    while True:
        action = input("❓ Do you want to add a new user (a) or update a existing password (u)? (q to exit): ").lower()
        if action == 'q':
            break
        elif action in ['a', 'u']:
            username = input("--Username: ")
            password = input("--Password: ")
            if action == 'a':
                add_user(username, password)
            else:
                update_password(username, password)
        else:
            print("⚠️ Invalid Input, try again.")
