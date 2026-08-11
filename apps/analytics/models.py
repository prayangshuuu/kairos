from django.db import models
from django.conf import settings

class PageView(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="page_views")
    event_type = models.ForeignKey("scheduling.EventType", on_delete=models.CASCADE, null=True, blank=True, related_name="page_views")
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    referrer = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        if self.event_type:
            return f"View for {self.user.email} - {self.event_type.slug} at {self.timestamp}"
        return f"View for {self.user.email} at {self.timestamp}"
