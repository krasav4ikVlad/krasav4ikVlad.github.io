<?php 
  require "includes/config.php";
?>
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Блог IT_Минималиста!</title>

  <!-- Bootstrap Grid -->
  <link rel="stylesheet" type="text/css" href="/media/assets/bootstrap-grid-only/css/grid12.css">

  <!-- Google Fonts -->
  <link href="https://fonts.googleapis.com/css?family=Roboto:300,400,500,700" rel="stylesheet">

  <!-- Custom -->
  <link rel="stylesheet" type="text/css" href="/media/css/style.css">
</head>
<body>

  <div id="wrapper">

    <?php 

      include "includes/headers.php";

      $articles = mysqli_query($connection, "SELECT * FROM articles WHERE id = " . (int) $_GET['id']);

      if ( mysqli_num_rows($articles) <= 0) {
          ?>

          <div class="block" id="comment-add-form">
                    <h3>Добавить статью</h3>
                    <div class="block__content">
                      <form class="form" method="POST" action="/article.php?id=<?php echo $art['id']?>">
                        <?php 
                          if (isset($_POST['do_post'])) {
                            $errors = array();

                            if($_POST['title'] == ''){
                              $errors = 'Введите Имя!';
                            }
                            if($_POST['nickname'] == ''){
                              $errors = 'Введите Никнейм!';
                            }
                            if($_POST['email'] == ''){
                              $errors = 'Введите почту!';
                            }
                            if($_POST['text'] == ''){
                              $errors = 'Введите текст комментария!';
                            }

                            if (empty($errors)) {
                              mysqli_query($connection, "INSERT INTO `articles` (`title`, `text`, `categories_id`) VALUES ('".$_POST['title']."', '".$_POST['text']."', '".$_POST['categories_id']."'");
                              echo '<span style="color: green; font-weight:bold; margin-bottom: 10px; display: block;"> Статья успешно добавлена! </span>';
                              
                            }
                          }

                         ?>
                        <div class="form__group">
                          <div class="row">
                            <div class="col-md-6">
                              <input type="text" class="form__control" required="Введите это поле!" name="title" placeholder="Название статьи">
                            </div>
                            <div class="col-md-6">
                            <div class="col-md-6">
                              <input type="text" class="form__control" required="Введите это поле!" name="categories_id" placeholder="Категория_id">
                            </div>
                          </div>
                        </div>
                        <br>
                        <div class="form__group">
                          <textarea name="text" required="Введите это поле!" class="form__control" placeholder="Текст статьи ..."></textarea>
                        </div>
                        <div class="form__group">
                          <input type="submit" class="form__control" name="do_post" value="Добавить статью">
                        </div>
                      </form>
                    </div>
                  </div>

        <?php
        
      } else {
        $art = mysqli_fetch_assoc($articles);
        mysqli_query($connection, "UPDATE articles SET views = views + 1 WHERE id = " . (int) $art['id']);
        ?>
          <div id="content">
            <div class="container">
              <div class="row">
                <section class="content__left col-md-8">
                  <div class="block">
                    <a>Просмотров: <?php echo $art['views']; ?></a>
                    <h3><?php echo $art['title'] ?></h3>
                    <div class="block__content">
                    <img src="/static/image/<?php echo $art['image']; ?>" style = "max-width: 100%;">
                      <div class="full-text"><?php echo $art['text'] ?></div>
                    </div>
                  </div>

                    <div class="block">
                      <h3>Комментарии</h3>
                      <div class="block__content">
                        <div class="articles articles__vertical">
                          <?php 
                            $comments_all = mysqli_query($connection, "SELECT * FROM comments WHERE articles_id = " . (int) $art['id'] . " ORDER BY 'id' DESC");

                            if ( mysqli_num_rows($comments_all) <= 0) {
                              echo 'Комментариев пока нет.';
                            }
                            while ($comment = mysqli_fetch_assoc($comments_all))
                            {
                          ?>
                              <article class="article">
                                <div class="article__image" style="background-image: url(https://gravatar.com/avatar/<?php echo md5($comment['email']); ?>?s=125);"></div>
                                <div class="article__info">
                                  <a href="/article.php?id=<?php echo $comment['articles_id']; ?>"><?php echo $comment['author'] ?></a>
                                  <div class="article__info__meta"></div>
                                  <div class="article__info__preview"><?php echo $comment['text']; ?></div>
                                </div>
                              </article>
                              <?php
                                }
                              ?>

                        </div>
                      </div>
                    </div>

                  <div class="block" id="comment-add-form">
                    <h3>Добавить комментарий</h3>
                    <div class="block__content">
                      <form class="form" method="POST" action="/article.php?id=<?php echo $art['id']?>">
                        <?php 
                          if (isset($_POST['do_post'])) {
                            $errors = array();

                            if($_POST['name'] == ''){
                              $errors = 'Введите Имя!';
                            }
                            if($_POST['nickname'] == ''){
                              $errors = 'Введите Никнейм!';
                            }
                            if($_POST['email'] == ''){
                              $errors = 'Введите почту!';
                            }
                            if($_POST['text'] == ''){
                              $errors = 'Введите текст комментария!';
                            }

                            if (empty($errors)) {
                              mysqli_query($connection, "INSERT INTO `comments` (`author`, `nickname`, `email`, `text`, `pubdate`, `articles_id`) VALUES ('".$_POST['name']."', '".$_POST['nickname']."', '".$_POST['email']."', '".$_POST['text']."', current_timestamp(), '".$art['id']."')");
                              echo '<span style="color: green; font-weight:bold; margin-bottom: 10px; display: block;"> Комментарий успешно добавлен! </span>';
                              
                            }
                          }

                         ?>
                        <div class="form__group">
                          <div class="row">
                            <div class="col-md-6">
                              <input type="text" class="form__control" required="Введите это поле!" name="name" placeholder="Имя" value="<?php echo $_POST['name'] ?>">
                            </div>
                            <div class="col-md-6">
                              <input type="text" class="form__control" required="Введите это поле!" name="nickname" placeholder="Никнейм" value="<?php echo $_POST['nickname'] ?>">
                            </div>
                            <div class="col-md-6">
                              <input type="text" class="form__control" required="Введите это поле!" name="email" placeholder="email" value="<?php echo $_POST['email'] ?>">
                            </div>
                          </div>
                        </div>
                        <div class="form__group">
                          <textarea name="text" required="Введите это поле!" class="form__control" placeholder="Текст комментария ..." value="<?php echo $_POST['text'] ?>"></textarea>
                        </div>
                        <div class="form__group">
                          <input type="submit" class="form__control" name="do_post" value="Добавить комментарий">
                        </div>
                      </form>
                    </div>
                  </div>

                </section>
                <section class="content__right col-md-4">
                  <?php
                    include "includes/side_bar.php"
                  ?>
                </section>
              </div>
            </div>
          </div>

        <?php
      }

     ?>

    

    <?php 

      include "includes/futer.php";

     ?>
  </div>

</body>
</html>