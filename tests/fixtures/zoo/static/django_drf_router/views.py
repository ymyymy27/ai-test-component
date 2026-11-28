from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response


class ProjectViewSet(viewsets.ModelViewSet):
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return Response({"archived": True})
