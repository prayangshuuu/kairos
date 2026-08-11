from unittest.mock import patch
class Creds:
    def __init__(self):
        self.token = "old"
    def refresh(self, req):
        self.token = "refreshed"

with patch('__main__.Creds.refresh', autospec=True) as mock_refresh:
    def side_effect(self, req):
        self.token = "new"
    mock_refresh.side_effect = side_effect
    c = Creds()
    c.refresh("req")
    print(c.token)
