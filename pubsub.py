
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub

class AsyncConn:
    def __init__(self, id: str, channel_name: str) -> None:
        config = PNConfiguration()
        config.subscribe_key = 'sub-c-7926c31d-f8fd-45e1-b1ea-3547d857274c'
        config.publish_key = 'pub-c-9b1959c5-4f1d-4ce6-b22e-66e5225235e5'
        config.user_id = id
        config.enable_subscribe = True
        config.daemon = True

        self.pubnub = PubNub(config)
        self.channel_name = channel_name

        print(f"Configurando conexão com o canal '{self.channel_name}'...")
        subscription = self.pubnub.channel(self.channel_name).subscription()
        subscription.subscribe()

    def publish(self, data: dict):
        print("tentando enviar uma mensagem")
        self.pubnub.publish().channel(self.channel_name).message(data).sync()

