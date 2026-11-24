import hmac
import re
import unicodedata

VEHICLE=re.compile(r'(?:[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]|[A-Z])[A-Z][0-9A-Z]{5,6}')


def token(raw,salt):
    clean=''.join(unicodedata.normalize('NFKC',raw).split()).upper().replace('·','')
    if not VEHICLE.fullmatch(clean):
        raise ValueError('PLATE')
    return hmac.digest(salt,clean.encode(),'sha256').hex()


def scrub_deep(value,salt):
    """Replace plate fields anywhere in an orchestration payload."""
    if isinstance(value,dict):
        plate=value.get('plate')
        if plate is not None:
            try:
                value.setdefault('vehicle',token(str(plate),salt))
            except ValueError:
                value['plate_status']='PLATE'
            value.pop('plate',None)
        fields=value.get('fields')
        if isinstance(fields,dict) and fields.get('plate') is not None:
            try:
                fields.setdefault('vehicle',token(str(fields['plate']),salt))
                value.setdefault('vehicle',fields['vehicle'])
            except ValueError:
                fields['plate_status']='PLATE'
            fields.pop('plate',None)
        for child in value.values():
            scrub_deep(child,salt)
    elif isinstance(value,list):
        for child in value:
            scrub_deep(child,salt)
    return value


def scrub(entries,salt):
    good,bad=[],[]
    for row in entries:
        fields=row['fields']
        clean=row | {'fields':{k:v for k,v in fields.items() if k!='plate'}}
        try:
            good.append(clean | {'vehicle':token(fields['plate'],salt)})
        except ValueError as error:
            bad.append(clean | {'code':str(error)})
    return good,bad
