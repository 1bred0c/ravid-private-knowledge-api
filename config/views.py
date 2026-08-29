from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
class HealthCheckSerializer(serializers.Serializer):
    status = serializers.CharField()


class HealthCheckView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=HealthCheckSerializer)
    def get(self, request):
        return Response({'status': 'ok'})
