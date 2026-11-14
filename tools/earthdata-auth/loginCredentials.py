

import earthaccess
import os

# Prompt for a username and password and store the token in a variable
auth = earthaccess.login()
token = earthaccess.get_edl_token()['access_token']

# Define the path for the .edl_token file in the home directory
token_file_path = os.path.join(os.path.expanduser("~"), ".edl_token")

# Write the token to the .edl_token file
with open(token_file_path, 'w') as token_file:
    token_file.write(token)

print(f"Token saved to {token_file_path}")

