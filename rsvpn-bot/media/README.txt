Картинки экранов бота.

Имя файла = ключ экрана. Расширение любое: .png, .jpg, .jpeg, .webp

  profile.png              профиль (главный экран)
  subscription.png         выбор тарифа
  subscription_active.png  действующая подписка
  no_funds.png             не хватает средств
  devices.png              менеджер устройств
  payment.png              выбор способа оплаты
  referrals.png            реферальная программа
  gifts.png                подарки
  bypass.png               белые списки (ByPass)

Файла нет — экран просто уйдёт текстом, ошибки не будет.
Проверить, что бот видит: python -m scripts.check_setup

ПЕРЕИМЕНОВЫВАТЬ НЕ ОБЯЗАТЕЛЬНО.

Файлы из старого проекта распознаются по своим именам, просто скопируйте их
сюда как есть:

  new_profile.png          -> экран профиля
  new_type_sub.png         -> выбор тарифа
  new_your_sub.png         -> действующая подписка
  new_no_funds.png         -> не хватает средств
  new_limit_devices.png    -> менеджер устройств
  new_your_devices.png     -> список устройств
  new_top_up.png           -> выбор оплаты
  new_referrals.png        -> реферальная программа
  new_gifts.png            -> подарки
  new_your_bypass_sub.png  -> белые списки

Полный список соответствий — в app/core/container.py, MEDIA_ALIASES.
