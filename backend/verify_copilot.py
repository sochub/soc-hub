import httpx
import sys

# Use backend service name for docker networking
BASE_URL = "http://backend:8000/api/v1"

def run():
    try:
        # Login
        print("\nLogging in...")
        r = httpx.post(f"{BASE_URL}/login/access-token", data={
            "grant_type": "",
            "username": "analyst@example.com", # Reuse existing user or creates error if not exists (handled)
            "password": "securepassword"
        })
        
        # If login fails, try creating user (if DB was reset)
        if r.status_code != 200:
            print("Login failed, attempting to create user...")
            r = httpx.post(f"{BASE_URL}/users/", json={
                "email": "analyst@example.com",
                "full_name": "Security Analyst",
                "role": "analyst",
                "is_active": True,
                "password": "securepassword"
            })
            # Login again
            r = httpx.post(f"{BASE_URL}/login/access-token", data={
                "grant_type": "",
                "username": "analyst@example.com",
                "password": "securepassword"
            })

        if r.status_code != 200:
             print(f"Login failed: {r.status_code} {r.text}")
             return

        token = r.json().get("access_token")
        print("Login successful.")
        headers = {"Authorization": f"Bearer {token}"}

        # Chat
        print("\nTesting Copilot Chat...")
        # Assume case ID 1 exists (created in previous verification)
        # If not, create one
        
        # Check if case 1 exists
        r = httpx.get(f"{BASE_URL}/cases/1", headers=headers)
        if r.status_code == 404:
             print("Case 1 not found, creating...")
             r = httpx.post(f"{BASE_URL}/cases/", json={
                "title": "Malware Outbreak",
                "description": "Ransomware detected on endpoint.",
                "status": "new",
                "severity": "critical"
            }, headers=headers)
             case_id = r.json()['id']
        else:
            case_id = 1

        payload = {
            "case_id": case_id,
            "messages": [{"role": "user", "content": "What should I do first?"}]
        }
        
        r = httpx.post(f"{BASE_URL}/copilot/chat", json=payload, headers=headers)
        print(f"Chat Response Code: {r.status_code}")
        print(f"Chat Response: {r.json()}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    run()
