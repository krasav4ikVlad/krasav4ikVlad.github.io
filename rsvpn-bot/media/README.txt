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

Из старого проекта соответствие такое:
  img/new_profile.png        -> media/profile.png
  img/new_type_sub.png       -> media/subscription.png
  img/new_your_sub.png       -> media/subscription_active.png
  img/new_no_funds.png       -> media/no_funds.png
  img/new_limit_devices.png  -> media/devices.png
  img/new_referrals.png      -> media/referrals.png
