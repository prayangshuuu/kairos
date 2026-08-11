from unittest.mock import patch
from google.oauth2.credentials import Credentials

def test():
    with patch('google.oauth2.credentials.Credentials.refresh', autospec=True) as mock_refresh:
        def side_effect(self, req):
            print(f"type is {type(self)}")
            self.token = "new"
        mock_refresh.side_effect = side_effect
        c = Credentials("old")
        c.refresh("req")
        print(c.token)

test()
