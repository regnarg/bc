#include "sqlite3ext.h"
SQLITE_EXTENSION_INIT1
#include <assert.h>
#include <string.h>


static void binxor_func(
    sqlite3_context *context,
    int argc,
    sqlite3_value **argv
){
    unsigned char *out;
    char *to_free = 0;
    int i;
    char temp[100];
    assert(argc == 2);
    if( sqlite3_value_type(argv[0])==SQLITE_NULL ) return;
    if( sqlite3_value_type(argv[1])==SQLITE_NULL ) return;

    const unsigned char *a = (const unsigned char*)sqlite3_value_blob(argv[0]);
    int a_len = sqlite3_value_bytes(argv[0]);
    const unsigned char *b = (const unsigned char*)sqlite3_value_blob(argv[1]);
    int b_len = sqlite3_value_bytes(argv[1]);

    if (a_len != b_len) return;

    if(a_len < sizeof(temp)-1) {
        out = temp;
    } else {
        out = to_free = sqlite3_malloc(a_len + 1);
        if (out==0) {
            sqlite3_result_error_nomem(context);
            return;
        }
    }
    for (int i=0; i<a_len; i++) {
        //fprintf(stderr, "'%c' ^ '%c' = '%c'\n", a[i], b[i]);
        out[i] = a[i] ^ b[i];
    }
    out[a_len] = 0;
    sqlite3_result_blob(context, (char*)out, a_len, SQLITE_TRANSIENT);
    sqlite3_free(to_free);
}

static void binshr_func(
    sqlite3_context *context,
    int argc,
    sqlite3_value **argv
){
    unsigned char *out;
    char *to_free = 0;
    int i;
    char temp[100];
    assert(argc == 2);
    if( sqlite3_value_type(argv[0])==SQLITE_NULL ) return;
    if( sqlite3_value_type(argv[1])==SQLITE_NULL ) return;

    const unsigned char *a = (const unsigned char*)sqlite3_value_blob(argv[0]);
    int a_len = sqlite3_value_bytes(argv[0]);
    const unsigned char *b = (const unsigned char*)sqlite3_value_blob(argv[1]);
    int b_len = sqlite3_value_bytes(argv[1]);

    if (a_len != b_len) return;

    if(a_len < sizeof(temp)-1) {
        out = temp;
    } else {
        out = to_free = sqlite3_malloc(a_len + 1);
        if (out==0) {
            sqlite3_result_error_nomem(context);
            return;
        }
    }
    for (int i=0; i<a_len; i++) {
        //fprintf(stderr, "'%c' ^ '%c' = '%c'\n", a[i], b[i]);
        out[i] = a[i] ^ b[i];
    }
    out[a_len] = 0;
    sqlite3_result_blob(context, (char*)out, a_len, SQLITE_TRANSIENT);
    sqlite3_free(to_free);
}



#ifdef _WIN32
__declspec(dllexport)
#endif
int sqlite3_extension_init(
    sqlite3 *db, 
    char **pzErrMsg, 
    const sqlite3_api_routines *pApi
){
    int rc = SQLITE_OK;
    SQLITE_EXTENSION_INIT2(pApi);
    (void)pzErrMsg;    /* Unused parameter */
    rc = sqlite3_create_function(db, "binxor", 2, SQLITE_UTF8, 0,
                                                             binxor_func, 0, 0);
    return rc;
}
