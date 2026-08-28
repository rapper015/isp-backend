from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from accounts.views import AdminAPIView

from . import services
from .serializers import SubscriberDetailSerializer, SubscriberSerializer


class SubscriberListCreateView(AdminAPIView):
    def get(self, request):
        subscribers = services.list_subscribers()
        return Response(SubscriberDetailSerializer(subscribers, many=True).data)

    def post(self, request):
        subscriber = services.create_subscriber(request.data)
        return Response(SubscriberSerializer(subscriber).data, status=HTTP_201_CREATED)


class SubscriberDetailView(AdminAPIView):
    def get(self, request, subscriber_id):
        subscriber = services.get_subscriber(subscriber_id)
        return Response(SubscriberDetailSerializer(subscriber).data)

    def patch(self, request, subscriber_id):
        subscriber = services.update_subscriber(subscriber_id, request.data)
        return Response(SubscriberSerializer(subscriber).data)

    def delete(self, request, subscriber_id):
        services.delete_subscriber(subscriber_id)
        return Response(status=HTTP_204_NO_CONTENT)
