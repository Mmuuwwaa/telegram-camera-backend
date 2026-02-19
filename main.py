        sent_message = await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=BufferedInputFile(photo_bytes, filename=f"photo_{user_id}.jpg"),
            caption=group_caption
        )
        file_id = sent_message.photo[-1].file_id

        # Служебное сообщение для бота
        if task_id:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"#task_report: {user_id}, {task_id}, {stage_display}, {on_time}, {file_id}"
            )
        else:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"#miniapp_report: {user_id}, {stage_display}, {on_time}, {file_id}"
            )

        # Уведомление пользователю
        try:
            if on_time:
                await bot.send_message(chat_id=user_id, text="✅ Ваше фото принято вовремя!")
            else:
                await bot.send_message(chat_id=user_id, text="⚠️ Фото принято, но вне временного окна.")
        except Exception as e:
            logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")