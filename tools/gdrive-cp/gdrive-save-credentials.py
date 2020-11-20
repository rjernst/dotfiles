import os
import sys

# parse params before intializing pydrive
if len(sys.argv) != 3:
    print("usage: gdrive-cp client_secrets.json credentials.txt")
    sys.exit(1)
secrets_file = sys.argv[1]
creds_file = sys.argv[2]
os.chdir(os.path.dirname(secrets_file))

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

gauth = GoogleAuth()
gauth.LoadClientConfigFile(secrets_file)
gauth.LoadCredentialsFile(creds_file)
if gauth.credentials is None:
    gauth.GetFlow()
    gauth.flow.params.update({'access_type': 'offline'})
    gauth.flow.params.update({'approval_prompt': 'force'})
    gauth.LocalWebserverAuth()
elif gauth.access_token_expired:
    gauth.Refresh()
else:
    gauth.Authorize()
gauth.SaveCredentialsFile(creds_file)

