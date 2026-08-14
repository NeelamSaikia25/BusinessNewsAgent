print ("================")
print ("  Business News Agent")
print ("================") 
    
print("\nChoose a news category:")
print("1. Technology")
print("2. Finance")
print("3. Business")

choice = input("\nEnter the number of your choice (1-3): ")

print("\nToday's Headlines")

if choice == "1":
    print("\nTechnology News:")
    print("- New AI breakthrough announced")
    print("- Major tech company releases innovative product")
elif choice == "2":
    print("\nFinance News:")
    print("- Stock market reaches new highs")
    print("- Economic indicators show positive growth")
elif choice == "3":
    print("\nBusiness News:")
    print("- Major acquisition announced")
    print("- Company reports strong quarterly earnings")
else:
    print("\nInvalid choice.")
    