#pragma once
// Bounded standalone JSON decoder: no peripheral or command side effect.
#include <array>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
namespace qwp {
enum class Kind {object,array,string,number,boolean,null_value};
struct Node {Kind kind=Kind::null_value;
std::string text;
int first=-1,next=-1,key=-1;
double value=0;
};
struct Json final {
    std::array<Node,128> nodes{};
    std::string_view input;
    std::size_t pos=0,used=0;
    bool ok=true;
    explicit Json(std::string_view bytes):input(bytes){}
    void ws(){while(pos<input.size()&&(input[pos]==' '||input[pos]=='\n'||input[pos]=='\r'||input[pos]=='\t'))++pos;
    }
    int alloc(Kind kind){if(used==nodes.size()){ok=false;
return -1;
}const auto i=static_cast<int>(used++);
nodes[i].kind=kind;
return i;
    }
    bool take(char c){ws();
if(pos==input.size()||input[pos++]!=c){ok=false;
return false;
}return true;
    }
    static void utf8(std::string& out,std::uint32_t c){
        if(c<0x80)out+=static_cast<char>(c);
        else if(c<0x800){out+=static_cast<char>(0xc0|(c>>6));
out+=static_cast<char>(0x80|(c&63));
        }
        else if(c<0x10000){out+=static_cast<char>(0xe0|(c>>12));
out+=static_cast<char>(0x80|((c>>6)&63));
out+=static_cast<char>(0x80|(c&63));
        }
        else{out+=static_cast<char>(0xf0|(c>>18));
out+=static_cast<char>(0x80|((c>>12)&63));
out+=static_cast<char>(0x80|((c>>6)&63));
out+=static_cast<char>(0x80|(c&63));
        }
    }
    std::uint32_t hex4(){
        std::uint32_t v=0;
        for(int i=0;
i<4;
++i){if(pos==input.size()){ok=false;
return 0;
}const char c=input[pos++];
v<<=4;
            if(c>='0'&&c<='9')v+=c-'0';
else if(c>='a'&&c<='f')v+=c-'a'+10;
else if(c>='A'&&c<='F')v+=c-'A'+10;
else{ok=false;
return 0;
            }}
        return v;
    }
    int string(){
        if(!take('"'))return -1;
        const auto i=alloc(Kind::string);
if(i<0)return -1;
        auto& out=nodes[i].text;
        while(ok&&pos<input.size()){
            const auto c=static_cast<unsigned char>(input[pos++]);
            if(c=='"')return i;
            if(c<0x20){ok=false;
break;
            }
            if(c=='\\'){
                if(pos==input.size()){ok=false;
break;
}const char e=input[pos++];
                if(e=='"'||e=='\\'||e=='/')out+=e;
                else if(e=='b')out+='\b';
else if(e=='f')out+='\f';
else if(e=='n')out+='\n';
else if(e=='r')out+='\r';
else if(e=='t')out+='\t';
                else if(e=='u'){
                    auto cp=hex4();
                    if(cp>=0xd800&&cp<=0xdbff){
                        if(pos+2>input.size()||input[pos++]!='\\'||input[pos++]!='u'){ok=false;
break;
                        }
                        const auto low=hex4();
if(low<0xdc00||low>0xdfff){ok=false;
break;
                        }
                        cp=0x10000+((cp-0xd800)<<10)+(low-0xdc00);
                    }else if(cp>=0xdc00&&cp<=0xdfff){ok=false;
break;
                    }
                    if(ok)utf8(out,cp);
                }else{ok=false;
break;
                }
            }else if(c<0x80)out+=static_cast<char>(c);
            else{
                int count=0;
std::uint32_t cp=0,minimum=0;
                if(c>=0xc2&&c<=0xdf){count=1;
cp=c&31;
minimum=0x80;
                }
                else if(c>=0xe0&&c<=0xef){count=2;
cp=c&15;
minimum=0x800;
                }
                else if(c>=0xf0&&c<=0xf4){count=3;
cp=c&7;
minimum=0x10000;
                }
                else{ok=false;
break;
                }
                for(int j=0;
j<count;
++j){if(pos==input.size()){ok=false;
break;
}const auto cc=static_cast<unsigned char>(input[pos++]);
if((cc&0xc0)!=0x80){ok=false;
break;
}cp=(cp<<6)|(cc&63);
                }
                if(!ok||cp<minimum||cp>0x10ffff||(cp>=0xd800&&cp<=0xdfff)){ok=false;
break;
}utf8(out,cp);
            }
            if(out.size()>1024){ok=false;
break;
            }
        }
        ok=false;
return -1;
    }
    int value(unsigned depth=1){
        ws();
if(depth>4||pos==input.size()){ok=false;
return -1;
        }
        const char c=input[pos];
if(c=='"')return string();
        if(c=='{'||c=='['){
            ++pos;
const bool object=c=='{';
const auto i=alloc(object?Kind::object:Kind::array);
if(i<0)return -1;
            int last=-1;
ws();
if(pos<input.size()&&input[pos]==(object?'}':']')){++pos;
return i;
            }
            while(ok){
                int key=-1;
if(object){key=string();
if(key<0)return -1;
                    for(int n=nodes[i].first;
n>=0;
n=nodes[n].next)if(nodes[nodes[n].key].text==nodes[key].text){ok=false;
return -1;
                    }
                    if(!take(':'))return -1;
                    }
                const auto child=value(depth+1);
if(child<0)return -1;
nodes[child].key=key;
                if(last<0)nodes[i].first=child;
else nodes[last].next=child;
last=child;
                ws();
if(pos==input.size()){ok=false;
return -1;
}const char end=input[pos++];
if(end==(object?'}':']'))return i;
if(end!=','){ok=false;
return -1;
                }
            }
            return -1;
        }
        for(const auto literal:{std::string_view("true"),std::string_view("false"),std::string_view("null")}){
            if(input.substr(pos,literal.size())==literal){pos+=literal.size();
const auto i=alloc(literal=="null"?Kind::null_value:Kind::boolean);
if(i>=0)nodes[i].text=literal;
return i;
            }
        }
        const auto start=pos;
if(input[pos]=='-')++pos;
        if(pos==input.size()){ok=false;
return -1;
        }
        if(input[pos]=='0')++pos;
        else if(input[pos]>='1'&&input[pos]<='9'){while(pos<input.size()&&input[pos]>='0'&&input[pos]<='9')++pos;
        }
        else{ok=false;
return -1;
        }
        bool integral=true;
        if(pos<input.size()&&input[pos]=='.'){integral=false;
++pos;
const auto at=pos;
while(pos<input.size()&&input[pos]>='0'&&input[pos]<='9')++pos;
if(at==pos){ok=false;
return -1;
        }}
        if(pos<input.size()&&(input[pos]=='e'||input[pos]=='E')){integral=false;
++pos;
if(pos<input.size()&&(input[pos]=='+'||input[pos]=='-'))++pos;
const auto at=pos;
while(pos<input.size()&&input[pos]>='0'&&input[pos]<='9')++pos;
if(at==pos){ok=false;
return -1;
        }}
        const auto i=alloc(Kind::number);
if(i<0)return -1;
auto& n=nodes[i];
n.text=std::string(input.substr(start,pos-start));
        if(integral){std::int64_t v=0;
const auto r=std::from_chars(n.text.data(),n.text.data()+n.text.size(),v);
if(r.ec!=std::errc()||r.ptr!=n.text.data()+n.text.size()){ok=false;
return -1;
        }}
        char* end=nullptr;
n.value=std::strtod(n.text.c_str(),&end);
if(!std::isfinite(n.value)||end!=n.text.c_str()+n.text.size()){ok=false;
return -1;
}return i;
    }
    bool parse(){const auto root=value();
ws();
return ok&&root==0&&nodes[0].kind==Kind::object&&pos==input.size();
    }
    const Node* get(std::string_view key,int object=0)const{
        for(int i=nodes[object].first;
i>=0;
i=nodes[i].next)if(nodes[i].key>=0&&nodes[nodes[i].key].text==key)return &nodes[i];
return nullptr;
    }
    int index(const Node* node)const{return static_cast<int>(node-nodes.data());
    }
};
} // namespace qwp
