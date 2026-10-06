"""Official lark-oapi builders and WSClient; no alternative AI model."""
import json
import logging
import threading
import time

class FeishuSDK:
    def __init__(self,client,models,sleep=time.sleep):
        self.client,self.models,self.sleep=client,models,sleep
    @classmethod
    def connect_client(cls,app_id,app_secret):
        import lark_oapi as lark
        from lark_oapi.api.im import v1
        client=lark.Client.builder().app_id(app_id).app_secret(app_secret).log_level(lark.LogLevel.ERROR).timeout(10).build()
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

    def receipt(self,message_id,emoji):
        from deploy.receipts import ReactionOutcome
        if emoji!='Get':raise ValueError('Receipt emoji restricted')
        m=self.models
        body=m.CreateMessageReactionRequestBody.builder().reaction_type(m.Emoji.builder().emoji_type(emoji).build()).build()
        request=m.CreateMessageReactionRequest.builder().message_id(message_id).request_body(body).build()
        try:response=self.client.im.v1.message_reaction.create(request)
        except Exception:return ReactionOutcome('safe_unknown')
        status=getattr(getattr(response,'raw',None),'status_code',200)
        if status==403 or getattr(response,'code',None)==99991672:return ReactionOutcome('permission_denied')
        if status>=500 or status==429:return ReactionOutcome('safe_unknown')
        if not response.success():return ReactionOutcome('rejected')
        reaction_id=getattr(getattr(response,'data',None),'reaction_id',None)
        if not isinstance(reaction_id,str) or not reaction_id:return ReactionOutcome('safe_unknown')
        return ReactionOutcome('confirmed',reaction_id)

def normalized(event):
    message,sender=event.event.message,event.event.sender
    return {'chat_type':message.chat_type,'sender_type':sender.sender_type,
            'sender_id':sender.sender_id.open_id,'chat_id':message.chat_id,
            'message_type':message.message_type,'message_id':message.message_id,
            'text':json.loads(message.content).get('text','') if message.message_type=='text' else ''}

def start_ws(app_id,app_secret,adapter,on_client=None):
    import lark_oapi as lark
    def on_message(event):
        # Callback promptly persists admission; delivery is a separate worker.
        try: adapter.inject(adapter.owner,normalized(event))
        except PermissionError: return  # unauthorized traffic is deliberately ignored
        # Persistence/parse errors propagate so SDK delivery can be retried.
    dispatcher=lark.EventDispatcherHandler.builder('','').register_p2_im_message_receive_v1(on_message).build()
    client=lark.ws.Client(app_id,app_secret,event_handler=dispatcher,log_level=lark.LogLevel.ERROR)
    # SDK errors may include signed WS URLs; never send them to service logs.
    logging.getLogger("Lark").disabled=True
    if on_client: on_client(client)
    client.start()

def delivery_worker(adapter,stop):
    while not stop.wait(1):
        try: adapter.drain()
        except Exception:
            logging.getLogger(__name__).error('Delivery state update failed; worker backing off')
            if stop.wait(30): return  # persistence failure: bounded worker frequency
