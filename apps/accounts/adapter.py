import requests
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.files.base import ContentFile
from apps.accounts.services import process_avatar

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        
        if sociallogin.account.provider == 'google':
            data = sociallogin.account.extra_data
            picture_url = data.get('picture')
            if picture_url:
                try:
                    response = requests.get(picture_url, timeout=10)
                    if response.status_code == 200:
                        content_file = ContentFile(response.content, name=f"{user.id}_avatar.jpg")
                        processed_file = process_avatar(content_file)
                        user.avatar.save(f"{user.id}_avatar.jpg", processed_file, save=True)
                except Exception:
                    pass
        return user
