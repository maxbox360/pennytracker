from rest_framework.authentication import SessionAuthentication


class CsrfSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        authentication = super().authenticate(request)
        if authentication is None:
            self.enforce_csrf(request)
        return authentication
