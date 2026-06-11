import httpx
import sys
import asyncio

# Use backend service name for docker networking
BASE_URL = "http://backend:8000/api/v1"

def run():
    try:
        # Create User
        print("Creating User...")
        try:
            r = httpx.post(f"{BASE_URL}/users/", json={
                "email": "analyst2@example.com",
                "full_name": "Security Analyst",
                "role": "analyst",
                "is_active": True,
                "password": "securepassword"
            })
            
            if r.status_code == 400 and "already exists" in r.text:
                print("User already exists, proceeding to login.")
            elif r.status_code != 200:
                 print(f"Failed to create user: {r.status_code} {r.text}")
            else:
                print("User created successfully.")

        except Exception as e:
            print(f"Error connecting: {e}")
            return

        # Login
        print("\nLogging in...")
        r = httpx.post(f"{BASE_URL}/login/access-token", data={
            "grant_type": "",
            "username": "analyst2@example.com",
            "password": "securepassword"
        })
        
        if r.status_code != 200:
            print(f"Login failed: {r.status_code} {r.text}")
            return

        token = r.json().get("access_token")
        print("Login successful.")
        
        headers = {"Authorization": f"Bearer {token}"}

        # Create Case
        print("\nCreating Case...")
        r = httpx.post(f"{BASE_URL}/cases/", json={
            "title": "Suspicious Login",
            "description": "Multiple failed login attempts detected.",
            "status": "new",
            "severity": "high"
        }, headers=headers)
        print(f"Case creation status: {r.status_code}")
        print(r.json())

        # List Cases
        print("\nListing Cases...")
        r = httpx.get(f"{BASE_URL}/cases/", headers=headers)
        print(f"List cases status: {r.status_code}")
        cases = r.json()
        print(f"Found {len(cases)} cases.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    run()
