<?php
/**
 * گالری محصولات — خواندن از tg_messages و نمایش مرتب
 * آدرس: https://stlkala.com/gallery.php
 * محصولات گروه‌بندی‌شده (product_folder) بالای صفحه، بقیه پیام‌ها پایین.
 */
header('Content-Type: text/html; charset=utf-8');

$DB_HOST = 'localhost';
$DB_NAME = 'elecset3_tgarchive';
$DB_USER = 'elecset3_tguser';
$DB_PASS = 'Tglo0qHPOrMVX-6OgzQvI!7x';

try {
    $pdo = new PDO(
        "mysql:host=$DB_HOST;dbname=$DB_NAME;charset=utf8mb4",
        $DB_USER, $DB_PASS,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
         PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
    );
} catch (PDOException $e) {
    http_response_code(500);
    exit('<h1 style="font-family:sans-serif">خطای اتصال دیتابیس</h1>');
}

$rows = $pdo->query(
    "SELECT id, channel, date, title, description, category, tags,
            has_media, media_name, media_path, media_url, product_folder, link
     FROM tg_messages
     ORDER BY CAST(id AS SIGNED) DESC"
)->fetchAll();

// ---------- گروه‌بندی ----------
$products = [];      // product_folder => [...]
$ungrouped = [];     // پیام‌های بدون پوشه محصول
foreach ($rows as $m) {
    if (!empty($m['product_folder'])) {
        $products[$m['product_folder']][] = $m;
    } else {
        $ungrouped[] = $m;
    }
}
// محصولات: جدیدترین (بزرگ‌ترین id عضو) اول
uasort($products, function ($a, $b) {
    return max(array_column($b, 'id')) <=> max(array_column($a, 'id'));
});

function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

function product_title(array $members): string {
    // عنوان محصول: اولین عنوان AI معنادار (غیر placeholderهای پایه/نام فایل خام)
    $bad = ['تصویر تلگرام', 'فایل تلگرام', 'ویدئو تلگرام', 'تصویر بدون توضیح',
            'سند نامشخص', 'بدون عنوان'];
    foreach ($members as $m) {
        $t = trim((string)$m['title']);
        if ($t && !in_array($t, $bad, true) && !preg_match('/^https?:/', $t)
            && !preg_match('/\.(jpe?g|png|zip|mp4|stl|3mf|rar|7z)$/i', $t)) {
            return $t;
        }
    }
    // آخرین راه: متن پست‌های متنی
    foreach ($members as $m) {
        $t = trim((string)$m['text']);
        if (mb_strlen($t) > 10) return mb_substr($t, 0, 60);
    }
    return (string)$members[0]['product_folder'];
}

function product_cover(array $members): ?string {
    // کاور: اولین عکس؛ نبود، اولین رسانه
    foreach ($members as $m) {
        if ($m['has_media'] === 'photo' && !empty($m['media_url'])) return $m['media_url'];
    }
    foreach ($members as $m) {
        if (!empty($m['media_url'])) return $m['media_url'];
    }
    return null;
}

function product_file(array $members): ?array {
    foreach ($members as $m) {
        $n = (string)$m['media_name'];
        if ($n && preg_match('/\.(zip|rar|7z|stl|3mf|obj|step|stp|gcode)$/i', $n)) {
            return $m;
        }
    }
    return null;
}

function media_icon(array $m): string {
    switch ($m['has_media']) {
        case 'photo': return '🖼';
        case 'video': return '🎬';
        case 'document': return '📄';
        default: return '💬';
    }
}
?>
<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>گالری محصولات — 3D مدل‌ها</title>
<style>
  :root {
    --bg: #0f1420; --card: #171f2e; --card2: #1c2537;
    --txt: #e8edf5; --muted: #8fa0b8; --accent: #4f8cff; --accent2: #7b5cff;
    --green: #2ecc71; --radius: 16px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--txt); font-family: Vazirmatn, Tahoma, sans-serif;
    line-height: 1.7; padding-bottom: 60px;
  }
  .hero {
    padding: 48px 20px 30px; text-align: center;
    background: linear-gradient(135deg, #16203a 0%, #101625 60%, #131a2b 100%);
    border-bottom: 1px solid #232f47;
  }
  .hero h1 { font-size: 28px; background: linear-gradient(90deg, var(--accent), var(--accent2));
    -webkit-background-clip: text; background-clip: text; color: transparent; }
  .hero p { color: var(--muted); margin-top: 8px; font-size: 14px; }
  .stats { display: flex; gap: 14px; justify-content: center; margin-top: 18px; flex-wrap: wrap; }
  .stat { background: var(--card); border: 1px solid #26324c; border-radius: 12px;
    padding: 10px 22px; font-size: 13px; }
  .stat b { color: var(--accent); font-size: 17px; margin-inline-start: 6px; }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
  h2.sec { margin: 36px 0 18px; font-size: 20px; display: flex; align-items: center; gap: 10px; }
  h2.sec::before { content: ''; width: 4px; height: 22px; border-radius: 4px;
    background: linear-gradient(180deg, var(--accent), var(--accent2)); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; }
  .card {
    background: var(--card); border: 1px solid #26324c; border-radius: var(--radius);
    overflow: hidden; transition: transform .2s, border-color .2s, box-shadow .2s;
    display: flex; flex-direction: column;
  }
  .card:hover { transform: translateY(-4px); border-color: var(--accent);
    box-shadow: 0 12px 30px rgba(79,140,255,.15); }
  .cover { position: relative; aspect-ratio: 4/3; background: var(--card2); overflow: hidden; }
  .cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cover .placeholder { width: 100%; height: 100%; display: flex; align-items: center;
    justify-content: center; font-size: 44px; color: #3a4a6b; }
  .badge-count { position: absolute; top: 10px; inset-inline-start: 10px;
    background: rgba(15,20,32,.8); backdrop-filter: blur(4px); border-radius: 999px;
    padding: 3px 12px; font-size: 12px; }
  .card .body { padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 8px; flex: 1; }
  .card h3 { font-size: 15px; line-height: 1.5; }
  .meta { color: var(--muted); font-size: 12px; display: flex; gap: 10px; flex-wrap: wrap; }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: auto; }
  .tag { background: #202c44; color: #9db4d8; border-radius: 999px; padding: 2px 10px; font-size: 11px; }
  .actions { display: flex; gap: 8px; margin-top: 10px; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px; text-decoration: none;
    border-radius: 10px; padding: 8px 14px; font-size: 13px; font-weight: 600;
    transition: filter .15s;
  }
  .btn:hover { filter: brightness(1.15); }
  .btn-dl { background: linear-gradient(90deg, var(--accent), var(--accent2)); color: #fff; }
  .btn-tg { background: #22304d; color: #a9c0e8; }
  .msg-item {
    background: var(--card); border: 1px solid #26324c; border-radius: 14px;
    padding: 14px 16px; display: flex; gap: 14px; align-items: flex-start;
  }
  .msg-item + .msg-item { margin-top: 12px; }
  .msg-item .ic { font-size: 22px; }
  .msg-item .body { flex: 1; min-width: 0; }
  .msg-item h4 { font-size: 14px; font-weight: 600; }
  .msg-item .meta { margin-top: 2px; }
  .thumb { width: 64px; height: 64px; border-radius: 10px; object-fit: cover; flex-shrink: 0; }
  .small-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
  footer { text-align: center; color: var(--muted); font-size: 12px; margin-top: 50px; }
  @media (max-width: 640px) { .hero h1 { font-size: 22px; } }
</style>
</head>
<body>

<div class="hero">
  <h1>🖨️ گالری مدل‌های سه‌بعدی</h1>
  <p>آرشیو خودکار محصولات کانال تلگرام — عنوان، دسته و برچسب‌ها با هوش مصنوعی</p>
  <div class="stats">
    <div class="stat">📦 محصولات <b><?= count($products) ?></b></div>
    <div class="stat">🗂 رسانه‌ها <b><?= count(array_filter($rows, fn($r) => $r['media_url'])) ?></b></div>
    <div class="stat">💬 کل پست‌ها <b><?= count($rows) ?></b></div>
  </div>
</div>

<div class="wrap">

<?php if ($products): ?>
  <h2 class="sec">📦 محصولات (گروه‌بندی‌شده با هوش مصنوعی)</h2>
  <div class="grid">
  <?php foreach ($products as $folder => $members): ?>
    <?php
      $title = product_title($members);
      $cover = product_cover($members);
      $file  = product_file($members);
      $photos = array_filter($members, fn($m) => $m['has_media'] === 'photo' && $m['media_url']);
      $tags = [];
      foreach ($members as $m) {
          $t = json_decode((string)$m['tags'], true) ?: [];
          foreach ($t as $tg) $tags[$tg] = true;
      }
      $tags = array_slice(array_keys($tags), 0, 4);
      $newest = max(array_column($members, 'id'));
    ?>
    <div class="card">
      <div class="cover">
        <?php if ($cover): ?>
          <img src="<?= h($cover) ?>" alt="<?= h($title) ?>" loading="lazy">
        <?php else: ?>
          <div class="placeholder">📦</div>
        <?php endif; ?>
        <span class="badge-count"><?= count($photos) ?> عکس</span>
      </div>
      <div class="body">
        <h3><?= h($title) ?></h3>
        <div class="meta">
          <span>#<?= h($newest) ?></span>
          <?php if ($file): ?><span><?= h($file['media_name']) ?></span><?php endif; ?>
        </div>
        <?php if ($tags): ?>
        <div class="tags"><?php foreach ($tags as $tg): ?><span class="tag"><?= h($tg) ?></span><?php endforeach; ?></div>
        <?php endif; ?>
        <div class="actions">
          <?php if ($file && $file['media_url']): ?>
            <a class="btn btn-dl" href="<?= h($file['media_url']) ?>?download=1" download>⬇ دانلود فایل</a>
          <?php endif; ?>
          <a class="btn btn-tg" href="https://t.me/<?= h($members[0]['channel']) ?>/<?= h($newest) ?>" target="_blank">تلگرام ↗</a>
        </div>
      </div>
    </div>
  <?php endforeach; ?>
  </div>
<?php endif; ?>

<?php if ($ungrouped): ?>
  <h2 class="sec">🗂 سایر رسانه‌ها (هنوز گروه‌بندی نشده)</h2>
  <div class="small-grid">
  <?php foreach (array_slice($ungrouped, 0, 40) as $m): ?>
    <div class="msg-item">
      <?php if ($m['has_media'] === 'photo' && $m['media_url']): ?>
        <img class="thumb" src="<?= h($m['media_url']) ?>" loading="lazy" alt="">
      <?php else: ?>
        <span class="ic"><?= media_icon($m) ?></span>
      <?php endif; ?>
      <div class="body">
        <h4><?= h(mb_substr(trim((string)$m['title'] ?: (string)$m['media_name'] ?: 'بدون عنوان'), 0, 60)) ?></h4>
        <div class="meta">
          <span>#<?= h($m['id']) ?></span>
          <?php if (!empty($m['media_url'])): ?>
            <a href="<?= h($m['media_url']) ?>?download=1" style="color:var(--accent)">دانلود</a>
          <?php endif; ?>
        </div>
      </div>
    </div>
  <?php endforeach; ?>
  </div>
<?php endif; ?>

<footer>
  داده‌ها از آرشیو خودکار کانال تلگرام — آخرین به‌روزرسانی: <?= date('Y/m/d H:i') ?>
</footer>

</div>
</body>
</html>
