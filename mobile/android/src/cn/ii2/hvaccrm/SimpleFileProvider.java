package cn.ii2.hvaccrm;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * 极简 ContentProvider：把 app 私有目录里的文件以 content:// 暴露给系统相机写入。
 *
 * 为什么需要它：Android 7 起，相机应用不能接收 file:// 的 EXTRA_OUTPUT（FileUriExposedException），
 * 而官方 FileProvider 在 AndroidX 里。测试版不想为一行代码拉进整套 AndroidX，
 * 于是自己实现 openFile 这一条最小路径。
 *
 * 安全：authority 为 exported=false + grantUriPermissions=true，只能被一次性的 intent 授权访问；
 * 路径做了穿越校验，只允许 cacheDir/camera/ 下的纯文件名。
 */
public class SimpleFileProvider extends ContentProvider {

    public static final String AUTHORITY = "cn.ii2.hvaccrm.files";
    private static final String DIR = "camera";

    /** 构造一个指向 cacheDir/camera 下新文件的 content:// URI 与对应 File。 */
    public static Uri newCameraUri(Context ctx, File outFile) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath(outFile.getName())
                .build();
    }

    public static File cameraDir(Context ctx) {
        File d = new File(ctx.getCacheDir(), DIR);
        if (!d.exists()) {
            //noinspection ResultOfMethodCallIgnored
            d.mkdirs();
        }
        return d;
    }

    /** 把 content URI 还原成真实文件；非法路径一律抛异常。 */
    private File resolve(Uri uri) {
        String name = uri.getLastPathSegment();
        if (name == null || name.isEmpty() || name.contains("/") || name.contains("..")) {
            return null;
        }
        return new File(cameraDir(getContext()), name);
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File f = resolve(uri);
        if (f == null) throw new FileNotFoundException("bad uri: " + uri);
        int flags = ParcelFileDescriptor.MODE_CREATE;
        if (mode.contains("w")) flags |= ParcelFileDescriptor.MODE_WRITE_ONLY;
        if (mode.contains("r")) flags |= ParcelFileDescriptor.MODE_READ_ONLY;
        if (mode.contains("t")) flags |= ParcelFileDescriptor.MODE_TRUNCATE;
        return ParcelFileDescriptor.open(f, flags);
    }

    /** 部分相机/图片选择器会 query 文件名与大小，缺了会写失败或显示成 "unknown"。 */
    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File f = resolve(uri);
        if (f == null) return null;
        String[] cols = projection != null ? projection
                : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
            else row[i] = null;
        }
        c.addRow(row);
        return c;
    }

    @Override
    public String getType(Uri uri) {
        return "image/jpeg";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("read/write by uri only");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        File f = resolve(uri);
        if (f != null && f.exists()) {
            //noinspection ResultOfMethodCallIgnored
            f.delete();
            return 1;
        }
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }
}
