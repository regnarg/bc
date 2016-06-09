
import std.stdio;
import std.string;
import core.sys.posix.sys.stat;


void main(string[] args) {
    stat_t stbuf;
    char[] buf;
    while (true) {
        string s = stdin.readln();
        if (!s) break;
        s = chomp(s);
        lstat(s.toStringz, &stbuf);
    }

}
