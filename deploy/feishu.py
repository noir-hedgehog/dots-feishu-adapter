"""Official lark-oapi builders and WSClient; no alternative AI model."""
import json
import threading
import time

class FeishuSDK:
    def __init__(self,client,models,sleep=time.sleep):
        self.client,self.models,self.sleep=client,models,sleep
    @classmethod
    def connect_client(cls,app_id,app_secret):
        import lark_oapi as lark
        from lark_oapi.api.im import v1
        client=lark.Client.builder().app_id(app_id).app_secret(app_secret).log_level(lark.LogLevel.ERROR).build()
        return cls(client,v1)
    def send(self,chat_id,text,uuid,reply_to=None):
        m=self.models
        content=json.dumps({'text':text},ensure_ascii=False)
        if reply_to:
            body=m.ReplyMessageRequestBody.builder().msg_type('text').content(content).uuid(uuid).reply_in_thread(False).build()
            request=m.ReplyMessageRequest.builder().message_id(reply_to).request_body(body).build()
            operation=self.client.im.v1.message.reply
        else:
            body=m.CreateMessageRequestBody.builder().receive_id(chat_id).msg_type('text').content(content).uuid(uuid).build()
            request=m.CreateMessageRequest.builder().receive_id_type('chat_id').request_body(body).build()
            operation=self.client.im.v1.message.create
        for attempt in range(3):
            try:
                response=operation(request)
            except Exception:
                if attempt==2: raise RuntimeError('Feishu transport failed') from None
                self.sleep(0.2 * 2**attempt)
                continue
            status=getattr(getattr(response,'raw',None),'status_code',200)
            if status==429 or status>=500:
                if attempt==2: raise RuntimeError('Feishu temporarily unavailable')
                self.sleep(0.2 * 2**attempt)
                continue
            break
        if not response.success() or not response.data or not response.data.message_id: raise RuntimeError('Feishu send rejected')
        return {'message_id':response.data.message_id}

def normalized(event):
    message,sender=event.event.message,event.event.sender
    return {'chat_type':message.chat_type,'sender_type':sender.sender_type,
            'sender_id':sender.sender_id.open_id,'chat_id':message.chat_id,
            'message_type':message.message_type,'message_id':message.message_id,
            'text':json.loads(message.content).get('text','') if message.message_type=='text' else ''}

def start_ws(app_id,app_secret,adapter):
    import lark_oapi as lark
    def on_message(event):
        # Callback promptly persists admission; delivery is a separate worker.
        try: adapter.inject(adapter.owner,normalized(event))
        except PermissionError: return  # unauthorized traffic is deliberately ignored
        # Persistence/parse errors propagate so SDK delivery can be retried.
    dispatcher=lark.EventDispatcherHandler.builder('','').register_p2_im_message_receive_v1(on_message).build()
    client=lark.ws.Client(app_id,app_secret,event_handler=dispatcher,log_level=lark.LogLevel.ERROR)
    client.start()

def delivery_worker(adapter,stop):
    while not stop.wait(1):
        try: adapter.drain()
        except Exception: pass  # no secret-bearing diagnostics; queue remains persistent
