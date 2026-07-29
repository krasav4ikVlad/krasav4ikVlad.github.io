import { Cell } from '@telegram-apps/telegram-ui';

/**
 * Cell, по которому можно нажать.
 *
 * TelegramUI рендерит `Component="button"` как обычный <button>, но не сбрасывает
 * браузерные стили — без этого строка получает светлый `buttonface` и ширину по
 * содержимому. Сброс живёт в `.cell-btn` (app.css) и подключается здесь одним местом.
 */
export function ClickCell({ className = '', ...props }) {
  return <Cell Component="button" className={`cell-btn ${className}`.trim()} {...props} />;
}
