from rest_framework import serializers

from customers.franchises import public_franchise_id

from .models import NasAuditLog, NasDevice


class ConnectionSerializer(serializers.Serializer):
    host=serializers.CharField(max_length=253)
    api_port=serializers.IntegerField(min_value=1,max_value=65535)
    api_protocol=serializers.ChoiceField(choices=("API","API_SSL"))
    api_username=serializers.CharField(max_length=128)
    api_password=serializers.CharField(write_only=True,trim_whitespace=False)
    verify_tls=serializers.BooleanField(default=True)
    connection_timeout=serializers.IntegerField(default=5,min_value=1,max_value=30)
    certificate_fingerprint=serializers.CharField(required=False,allow_blank=True)
    ca_certificate=serializers.CharField(required=False,allow_blank=True,write_only=True)


class NasWriteSerializer(ConnectionSerializer):
    confirm=serializers.BooleanField()
    franchise_id=serializers.IntegerField()
    name=serializers.CharField(max_length=128)
    short_name=serializers.CharField(max_length=64,required=False,allow_blank=True)
    description=serializers.CharField(required=False,allow_blank=True)
    nas_type=serializers.CharField(default="mikrotik",max_length=64)
    radius_source_ip=serializers.IPAddressField(protocol="both")
    radius_secret=serializers.CharField(write_only=True,trim_whitespace=False)
    radius_auth_port=serializers.IntegerField(default=1812,min_value=1,max_value=65535)
    radius_accounting_port=serializers.IntegerField(default=1813,min_value=1,max_value=65535)
    coa_port=serializers.IntegerField(default=3799,min_value=1,max_value=65535)
    radius_services=serializers.ListField(child=serializers.ChoiceField(choices=("ppp","pppoe","hotspot","login","wireless","dhcp")),allow_empty=False)

    def validate_confirm(self,value):
        if value is not True: raise serializers.ValidationError("Explicit confirmation is required.")
        return value


class NasPatchSerializer(serializers.Serializer):
    name=serializers.CharField(max_length=128,required=False)
    short_name=serializers.CharField(max_length=64,required=False,allow_blank=True)
    description=serializers.CharField(required=False,allow_blank=True)
    host=serializers.CharField(max_length=253,required=False)
    radius_source_ip=serializers.IPAddressField(protocol="both",required=False)
    api_port=serializers.IntegerField(min_value=1,max_value=65535,required=False)
    api_protocol=serializers.ChoiceField(choices=("API","API_SSL"),required=False)
    api_username=serializers.CharField(max_length=128,required=False)
    api_password=serializers.CharField(write_only=True,required=False,trim_whitespace=False)
    radius_secret=serializers.CharField(write_only=True,required=False,trim_whitespace=False)
    verify_tls=serializers.BooleanField(required=False)
    connection_timeout=serializers.IntegerField(min_value=1,max_value=30,required=False)
    certificate_fingerprint=serializers.CharField(required=False,allow_blank=True)
    ca_certificate=serializers.CharField(required=False,allow_blank=True,write_only=True)
    enabled=serializers.BooleanField(required=False)
    radius_services=serializers.ListField(child=serializers.ChoiceField(choices=("ppp","pppoe","hotspot","login","wireless","dhcp")),required=False)


class NasSerializer(serializers.ModelSerializer):
    id=serializers.UUIDField(source="public_id",read_only=True)
    host=serializers.CharField(source="nas_ip_address",read_only=True)
    franchise_id=serializers.SerializerMethodField()
    health=serializers.JSONField(source="cached_health",read_only=True)
    class Meta:
        model=NasDevice
        fields=("id","franchise_id","name","short_name","description","vendor","nas_type","host","radius_source_ip","api_port","api_protocol","api_username","radius_auth_port","radius_accounting_port","coa_port","routeros_version","architecture","board_name","serial_number","system_identity","lifecycle_status","last_connection_at","last_sync_at","last_error_code","last_error_message","connection_timeout","verify_tls","certificate_fingerprint","enabled","selected_radius_services","health","created_at","updated_at")

    def get_franchise_id(self,obj): return public_franchise_id(obj.franchise)


class NasAuditSerializer(serializers.ModelSerializer):
    user_id=serializers.IntegerField(read_only=True)
    class Meta:
        model=NasAuditLog
        exclude=("nas","franchise")
