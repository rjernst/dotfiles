import os
import sys
from oauth2client.service_account import ServiceAccountCredentials

# parse params before intializing pydrive
if len(sys.argv) != 4:
    print("usage: gdrive-cp service_account.json src_path dest_dir_id")
    sys.exit(1)
secrets_file = sys.argv[1]
source_file = sys.argv[2]
dest_id = sys.argv[3]


print("Running gdrive copy script")

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

gauth = GoogleAuth()
scope = ['https://www.googleapis.com/auth/drive']
gauth.credentials = ServiceAccountCredentials.from_json_keyfile_name(secrets_file, scope)
drive = GoogleDrive(gauth)

dest = drive.CreateFile({'title': os.path.basename(source_file), 'parents': [{'id': dest_id}]})
dest.SetContentFile(source_file)
dest.Upload()
